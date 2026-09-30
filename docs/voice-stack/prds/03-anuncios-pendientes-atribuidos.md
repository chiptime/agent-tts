# PRD 03 — Anuncios pendientes atribuidos

> **Estado: DRAFT.** Trazado a D1 (hito 3), D2, D5, D6, D7, D8, D9. Requiere Hito 1.
> Diseño técnico preciso: **LOCKED en `../TECHNICAL-PLAN.md` T7–T8** (cola pendiente del
> host en SQLite, jerarquía de overflow, época, dispatcher, superficie PWA/CLI);
> ejecución: `../TASKS.md` VS3.*. Cobertura/gates: README D4 (Python/JS) y **D9 (Bash:
> líneas + matriz de alternativas)** — el delta Bash de este hito es MÍNIMO por diseño
> (T7 delega la lógica a `lib/pending_queue.py`); tooling T12; evidencias `../EXECUTION.md` §4–§6.
> **Fuentes (D8):** brain `hosts/herdr/brain`; motor `engine/src/agent_tts`; host
> `hosts/herdr/tts-plugin`. Líneas re-verificadas en HEAD `384dd4f` (2ª revisión 2026-09-30);
> re-verificar vigente al lanzamiento.

## 1. Contexto y problema

Los anuncios ambientales (agente terminó/bloqueado) se pierden hoy en dos puntos:

- **PC:** el watcher de herdr-tts sintetiza el anuncio y, si hay reproducción local activa
  (`is_playing` = lock existe + PID vivo), lo OMITE localmente con el mensaje "another chat is
  already speaking locally (omitted to avoid overlap)" — `bin/herdr-tts:3197-3200`, `is_playing` en
  `bin/herdr-tts:1377-1383`, catálogo en `bin/herdr-tts:333`. El coste de síntesis ya se pagó y el
  evento no se recupera: descarte silencioso local.
- **Teléfono:** la cola de la PWA se vacía en `stopAudio` (`static/app.js:1404-1417`, canónico) y el
  camino de error de un anuncio lo descarta "con texto" (`app.js:1339-1361,1419-1434`): queda texto
  visible pero sin reproducción ni registro recuperable estructurado.

Precedentes verificados que se reutilizan: ventana de asentamiento que descarta "done" intermedios
(`bin/herdr-tts:77-82,331,3096`), `COALESCE` de la cola del motor que fusiona eventos repetidos en
ventana (`engine/src/agent_tts/queue_manager.py:722-738`), y anuncios ya atribuidos de nacimiento
(`{pane_id, agent, status, label, text, audio_url}` — `watcher.py:194-221`, SSE en `server.py:396-433`).

**Dos dominios que este PRD mantiene separados:** (a) *jobs de respuesta* de conversación — objetos
de la cancelación por solicitud del Hito 1, con ciclo de vida corto; (b) *registros de anuncio
ambiental* — hechos observados del sistema (fin/bloqueo) que deben conservarse como información
atribuida aunque su reproducción se difiera. Confundirlos causó la inconsistencia anterior entre
"stop cancela" y "los anuncios se conservan": el stop del teléfono cancela el SPEECH del job de
respuesta y NO re-encola automáticamente ese speech cancelado; los REGISTROS ambientales siguen
recuperables sin reproducción hasta una acción deliberada permitida.

## 2A. Objetivos y no-objetivos

**Objetivos (D2)**

1. Conservar los eventos pendientes de fin/bloqueo SIN interrumpir la reproducción activa.
2. Consolidar estados repetidos de la misma sesión (mismo `pane_id`+estado) como metadatos de un
   registro único, distinguibles de los resultados terminales.
3. A capacidad: registro visible y recuperable; el desborde debe poder reproducirse después
   **regenerando** el habla aunque el mp3 original ya se haya recolectado (GC).
4. Especificar orden, manejo de sesión/época obsoleta y recuperación ante caída del host — sin
   garantizar "exactamente una vez" de habla física tras caída: la entrega incierta se marca y se
   resuelve de forma visible.
5. Almacenamiento acotado con comportamiento observable explícito; protección de pendientes y
   regeneración SIN política general de retención nueva.

**No-objetivos (D2/D7)**

- Exclusividad global multi-dispositivo o silenciar el PC.
- Política general de retención/borrado (D7); la GC vigente de mp3 `ann-*` >1h (`watcher.py:260-274`)
  se respeta — los pendientes se protegen regenerándose desde su texto, no reteniendo medios por
  tiempo indefinido.
- Reproducir anuncios por encima de la voz activa (no-interrupción es parte del requisito).
- Nueva capacidad/ventana/límite fijos de producto: todos esos números son parámetros técnicos
  ajustables (D2); este PRD no introduce cotas arbitrarias nuevas.

## 2B. Arquitectura y límites de confianza

- **PC:** la decisión de "omitir" del watcher (`bin/herdr-tts:3197-3200`) se sustituye por
  una cola pendiente persistente del host **[PROPUESTA — LOCKED en T7]**: SQLite WAL
  `~/.local/state/herdr-tts/pending.db`, lógica en `lib/pending_queue.py` (delta Bash =
  UN punto de delegación), que respeta el busy-check existente como señal de "diferir",
  no de "tirar". La reproducción diferida pasa `identifiers` por la superficie de
  playback/daemon actual (`daemon.py` enqueue), sin inventar un árbitro global nuevo.
- **Teléfono:** los registros de anuncio ganan estado persistente por dispositivo
  **[PROPUESTA — LOCKED en T8]**: `localStorage["herdr.speech.pending.v1"]` (tope 100 +
  overflow visible) para sobrevivir recarga/cierre.
- **Atribución/época:** `pane_id`+`agent`+`status` ya viajan; la consolidación se keyed por
  sesión y estado con época **[PROPUESTA — LOCKED en T7: `pane_pid` de tmux; cambio de
  `pane_pid` ⇒ época nueva ⇒ no terminales a `expired` visible; sin `pane_pid` ⇒ flag
  `epoch_unverified` visible]**.
- **Regeneración:** el registro conserva texto+atribución (recortado como hoy); el audio se
  re-sintetiza bajo demanda al reproducir si el mp3 ya no existe (GC `ann-*` >1 h intacta).
  Nada retiene medios por indefinido: dos niveles acotados (LOCKED T7) — cola ACTIVA
  despachable (256) y LEDGER durable de evidencia (8 MiB): la cola activa llena DESPLAZA
  al ledger (`evicted` visible, recuperable); el agotamiento del LEDGER/almacenamiento es
  `admission-blocked` duro y visible con sobre de emergencia (razón+atribución, SIN
  prometer detalle recuperable cuando la escritura durable es imposible) — jamás pérdida
  silenciosa, jamás garantía de almacenamiento detallado infinito bajo disco finito, y la
  reproducción del PC jamás se mutea por backpressure.
- Límites de confianza: estados locales del usuario en sus dispositivos; sin envío a
  terceros ni sync cloud; el texto ya recortado por `announce_max_chars`
  (`watcher.py:223-250`) se sanitiza igual en el registro persistente. La regeneración usa
  la política de proveedor EXPLÍCITA vigente del operador (no se afirma "local-only":
  los proveedores cloud ya permitidos siguen permitidos; sin red nueva implícita).

## 2C. Esquema de datos y máquina de estados

Registro **[PROPUESTA]** — los metadatos de repetición son datos del registro, no estados:

```json
{
  "id": "<uuid>", "epoch": "<session_epoch>", "pane_id": "...", "agent": "...",
  "status": "done|blocked", "label": "...", "text": "...",
  "audio_path": "...|null (regenerable)",
  "first_seen_ts": 0.0, "last_seen_ts": 0.0, "repeat_count": 1,
  "state": "pending|announcing|announced|uncertain|expired|evicted|cancelled"
}
```

FSM por registro **[PROPUESTA]** — estados finitos, transiciones explícitas:

```
                    ┌─(nuevo evento misma clave en ventana: repeat_count++, last_seen_ts≈now; el estado NO cambia)─┐
                    │                                                                                │
 nuevo evento ──▶ pending ──(canal libre / turno de reproducción)──▶ announcing ──(fin confirmado)──▶ announced (terminal)
                    │                                                     │
                    │                                                     ├──(caída/reconexión con entrega sin confirmar)──▶ uncertain
                    ├──(época obsoleta por reutilización de pane)──▶ expired (visible, no reproducible)
                    ├──(capacidad llena, desalojo del más viejo)──▶ evicted (visible; reproducible bajo demanda regenerando)
                    └──(cancelación explícita aplicable a ESTE registro)──▶ cancelled
 resolving: uncertain ──(acción deliberada permitida: reintentar / marcar anunciado / descartar)──▶ pending | announced | expired
```

Reglas duras de la FSM **[aristas antes ambiguas, ahora LOCKED en T7]**:

- **Sin replay ciego:** `uncertain` NUNCA se auto-reproduce al arrancar (no se puede garantizar
  exactly-once de habla física tras crash); exige acción deliberada y queda visible entretanto.
- `evicted` conserva texto+atribución y PUEDE reproducirse después regenerando el habla desde
  `text` aunque el mp3 haya pasado por la GC — cero pérdida silenciosa sin retención indefinida.
- `cancelled` procede sólo de una cancelación explícita aplicable al registro (p. ej. del Hito 1
  cuando el registro está ligado a una solicitud cancelable). El stop del teléfono sobre un JOB de
  respuesta NO re-encola ese speech cancelado NI cancela registros ambientales.
- `announcing` huérfano al rearrancar → `uncertain` (idempotente), nunca `announced` supuesto.
- **Repetición durante `announcing`:** sólo metadatos (`repeat_count++`, `last_seen_ts`), sin
  cambio de estado.
- **Repetición tras terminal (`announced`/`expired`/`evicted`/`cancelled`):** el terminal NUNCA se
  reabre; un evento genuinamente nuevo (nueva transición observada fuera de la ventana de
  consolidación de 60 s) crea registro NUEVO con `announce_seq` siguiente de la misma clave.

## 2D. Seguridad y modelo de amenazas

- El estado persistente local MUST sanitizar/acotar el texto por registro y acotar el presupuesto
  de disco total (tope configurable con comportamiento observable al alcanzarlo: `evicted` visible).
- Los registros del teléfono son datos de dispositivo: sin sync cloud implícita; su borrado obedece
  reglas existentes, no una política nueva (D7).
- La regeneración re-sintetiza bajo demanda; no amplia superficie de red nueva (misma cadena local).

## 2E. Métricas y criterios de aceptación

- **Aceptación (D2/D6):** en pruebas, NINGÚN evento fin/bloqueo se pierde sin registro visible
  (incluidos `evicted` y `expired`); repeticiones de la misma sesión producen exactamente un anuncio
  audible consolidado; el orden de reproducción respeta `first_seen_ts` de forma determinista;
  tras kill -9/rearranque los `announcing` quedan `uncertain` visibles sin replay automático;
  el desborde se reproduce bajo demanda con audio regenerado aunque el mp3 ya no exista.
- **No-regresión:** canales independientes; el PC nunca se silencia para "resolver" colisiones.

## 3. Requisitos (RFC 2119) con trazabilidad y evidencia

| FR | Requisito | Traza | Evidencia / supuesto |
|----|-----------|-------|----------------------|
| FR-01 | Los anuncios ambientales pendientes MUST conservarse sin interrumpir la reproducción activa. | D2 | Gap verificado: `bin/herdr-tts:3197-3200` omite |
| FR-02 | Estados repetidos de la misma sesión MUST consolidarse como metadatos (`repeat_count`, `last_seen`) de UN registro único atribuido, distinguibles de los resultados terminales. | D2 | Precedentes: settle `bin/herdr-tts:331`; COALESCE `queue_manager.py:722-738` |
| FR-03 | A capacidad, el sistema MUST desalojar dejando registro visible y recuperable; el descarte silencioso MUST NOT ocurrir. | D2 | Restricción de producto |
| FR-04 | El desalojo/reproducción diferida MUST poder regenerar el habla desde el texto persistido aunque el mp3 haya sido recolectado por la GC vigente; el almacenamiento total MUST ser acotado con comportamiento observable, sin retención indefinida de medios. | D2, D7 | GC vigente `watcher.py:260-274`; [PROPUESTA] de mecanismo |
| FR-05 | Cada registro MUST llevar atribución y época de sesión; la reutilización de pane MUST invalidar lo obsoleto a `expired` visible. | D2 | Atribución existente `watcher.py:194-221`; época [PROPUESTA] |
| FR-06 | El orden de reproducción de pendientes MUST ser determinista y especificado (FIFO por `first_seen_ts` con las prioridades existentes si aplican). | D2 | [PROPUESTA] |
| FR-07 | La política de diferimiento en PC MUST reutilizar el busy-check existente como señal de diferir y el enqueue del daemon para reproducir. | D1, D2 | `bin/herdr-tts:1377-1383`; `daemon.py:599` |
| FR-08 | El stop del teléfono MUST cancelar el speech del job de respuesta SIN re-encolarlo automáticamente; los registros ambientales MUST permanecer recuperables sin reproducción hasta una acción deliberada permitida. Jobs de respuesta y registros ambientales MUST modelarse como dominios separados. | D2 + corrección 2026-09-30 | Gap: `app.js:1404-1417` |
| FR-09 | Ante caída/reconexión con entrega sin confirmar, el registro MUST pasar a `uncertain` visible; el sistema MUST NOT reclamar exactly-once de habla física ni reproducir a ciegas lo ya posiblemente audible. | D2 | Corrección FSM 2026-09-30 |
| FR-10 | La resolución de `uncertain` y la reproducción de `evicted` MUST requerir acción deliberada permitida y quedar registradas. | D2 | [PROPUESTA] |
| FR-11 | Capacidad, ventana de consolidación y tope de almacenamiento SHOULD parametrizarse sin introducir cotas arbitrarias nuevas de producto. | D2 | D2: diseño técnico ajustable |
| FR-12 | El comportamiento MUST mantener la independencia de canales PC/teléfono (sin mute cruzado). | D2, D7 | `server.py:273-276` (always-on by design) |

## 4. Escenarios de prueba y resultado observable esperado

- **Unit (host herdr-tts):** con busy activo simulado (lock+PID falsos), el evento queda `pending`
  (no omitido); 3 repeticiones → 1 registro `pending` con `repeat_count=3` (metadatos, sin cambio de
  estado); época nueva → viejos a `expired` visibles; capacidad N+1 → el más viejo a `evicted` con
  registro íntegro.
- **Unit (PWA, node --test):** `stopAudio` NO re-encola el speech cancelado del job de respuesta Y
  conserva los registros ambientales en el almacenamiento elegido; recuperación tras "recarga".
- **Integración (PC):** daemon + host reales: reproducción larga + ráfaga de eventos → los
  pendientes suenan después en orden y consolidados; kill -9 a mitad de `announcing` → al
  rearrancar queda `uncertain` visible SIN replay automático; la acción deliberada lo resuelve;
  desborde + GC de mp3 → reproducción diferida regenera el audio desde el texto.
- **Integración (brain):** SSE entrega cada evento una vez atribuido; la GC de `ann-*` no destruye
  la recuperabilidad de pendientes (el texto vive en el registro; el audio se regenera).
- **Carreras/exactas:** finish-vs-cancel concurrentes (resultado terminal único, sin doble
  transición); reconexión de transporte durante `announcing`; crash/overflow/reutilización de pane
  (época) — todos con estado final inspeccionable según FSM.
- **E2E navegador (D6):** anuncio durante respuesta hablada en curso → no interrumpe, queda
  `pending`, suena al terminar en orden; stop del teléfono durante una respuesta → speech cancelado
  no re-encolado, registros ambientales intactos y visibles; múltiples anuncios repetidos → un solo
  toast/voz consolidado; recarga de PWA a mitad → registros restaurados; desborde → `evicted`
  visible + reproducción deliberada regenerada.
- **Manual físico:** audibilidad real de la cola diferida en auricular/Bluetooth (`EXECUTION.md` §8).
