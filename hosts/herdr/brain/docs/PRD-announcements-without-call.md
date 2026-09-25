# PRD — Anuncios sin llamada y con la app cerrada

| | |
|---|---|
| **Product** | herdr-brain (voice-assistant PWA) |
| **Status** | Aprobado — entrega 1 planificada, implementación pendiente |
| **Date** | 2026-09-25 |
| **Scope** | Fase 1: implementar. Fase 2: solo documentada, no se aborda. |
| **Effort** | Fase 1: medio (cliente `app.js` + `sw.js`, servidor Web Push) |
| **Code refs** | Líneas referidas al commit `ab20f8d` |

---

## 1. Contexto y problema

### P1 — Con la página abierta y sin llamada no llega ningún anuncio

Los anuncios de cambio de estado de los agentes solo empiezan a llegar al
iniciar una llamada. El usuario espera que suenen siempre que la página esté
abierta.

Causa verificada: el canal ya existe y ya cubre todos los agentes, pero el
cliente solo se suscribe dentro de `startCall`:

- Servidor: SSE `GET /events` (`src/herdr_brain/server.py:396`), alimentado por
  `AnnouncementHub` (`src/herdr_brain/watcher.py:48`).
- El watcher publica transiciones `done` y `blocked` (`watcher.py:39`) de
  **todos** los panes, sin filtro por sesión seleccionada (`watcher.py:174`).
- Cliente: `openEvents()` (`src/herdr_brain/static/app.js:1500`) tiene un único
  llamador, `startCall` (`app.js:2415`), con el comentario
  `// user gesture: unlocks autoplay for announcements`.

Defecto adicional: si `player.play()` es rechazado (autoplay, error de media),
el cliente oculta el toast y pasa al siguiente (`app.js:1294-1296`). El anuncio
se pierde en silencio.

### P2 — Con la app cerrada o en segundo plano no te enteras

Observado en el móvil: en segundo plano o con la pantalla bloqueada los anuncios
funcionan un momento y después dejan de llegar. Android congela las pestañas en
segundo plano, lo que corta la conexión SSE.

La alternativa de apoyarse en ntfy + MacroDroid/Tasker se descarta como
solución de producto: exige a cada usuario **dos apps adicionales** y
configuración manual, y no existe en iOS (MacroDroid y Tasker son solo
Android). La solución debe vivir **dentro de la propia PWA**.

### P3 — El anuncio no identifica la sesión

La etiqueta del anuncio es `agent_label` = tipo de agente + nombre de la carpeta
(o el título si no hay carpeta) (`watcher.py:106-113`), p. ej.
"opencode dotfiles". No usa el nombre de la sesión de Herdr ni su workspace:
dos sesiones del mismo agente en la misma carpeta producen anuncios idénticos.
En cambio, el selector de agentes de la PWA sí identifica la sesión: nombre =
título de Herdr (`agentDisplayName`, `app.js:770`) y workspace en gris
(`agentWorkspaceSub`, `app.js:779`).

### Límite de plataforma que condiciona el diseño

Una PWA cerrada no puede reproducir voz por sí sola. Las notificaciones Web
Push se procesan en el service worker, que no tiene acceso a audio; el sistema
solo emite su tono de notificación, nunca un mp3 propio
(<https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/showNotification>).
Por tanto, con la app cerrada el diseño es **notificación + un toque para
escuchar**. La voz sin tocar nada en segundo plano solo es posible manteniendo
la página viva (Fase 2).

## A. Objetivos y no-objetivos

### Objetivos (Fase 1)

1. **App abierta**: cada anuncio suena **en el momento en que llega**, haya o no
   llamada, de todos los agentes.
2. **App cerrada, en segundo plano o pantalla bloqueada**: llega una
   notificación de la propia PWA; **un toque** abre la app y reproduce la voz.
3. Sin apps adicionales: todo funciona con la PWA instalada, en Android y en
   iOS 16.4+.
4. Ningún anuncio se pierde en silencio con la app abierta: si no puede sonar,
   se ve.
5. Cada anuncio de la PWA identifica la sesión igual que el selector:
   **nombre de la sesión + workspace + estado**.

### No-objetivos

- **Voz sin tocar nada con la app en segundo plano.** Queda documentada como
  Fase 2 (§ Fase 2) y no se aborda.
- **App nativa** o wrapper nativo.
- **Integración con ntfy + MacroDroid/Tasker** como parte del producto.
- **Cambios en `herdr-tts`** ni en su push de ntfy: sigue funcionando y
  nombrando las sesiones como hasta ahora, de forma independiente, para quien
  lo tenga configurado.
- **Deduplicación entre canales.** Duplicados aceptados por el usuario:
  - PWA abierta en el PC → suena en la PWA y en los altavoces de `herdr-tts`.
  - Quien tenga ntfy configurado recibe su push además de la Web Push.
  - Con la app abierta, la notificación Web Push llega igualmente (OQ-2).
- **Multiusuario, autenticación e instalación para usuarios externos.** Hoy
  herdr-brain es de un solo usuario y se sirve por Tailscale. Abrirlo a usuarios
  externos es una decisión y un PRD aparte; este PRD solo evita añadir
  dependencias de apps externas.
- Replay de anuncios emitidos mientras el SSE estaba desconectado
  (`AnnouncementHub` no guarda historial, `watcher.py:69-81`); la Web Push cubre
  ese hueco.
- Reproducir con retraso los anuncios que el navegador bloqueó con la app
  abierta: suenan al llegar o se quedan como texto.
- Cambios en el comportamiento de anuncios **durante** una llamada.

## B. Arquitectura y fronteras

```
AnnouncementWatcher (poll 4 s, transiciones done/blocked)
  └─ anuncio {label, aviso, text, audio_url, pane_id, status}
       ├─ AnnouncementHub → GET /events (SSE) ──▶ PWA ABIERTA
       │     ├─ play() permitido → suena al llegar + toast
       │     ├─ play() bloqueado → toast persistente + "🔊 Activar voz"
       │     └─ mute activo → solo toast
       │
       └─ PushSender (NUEVO) → servicio push del navegador (FCM / Apple)
             ──▶ service worker de la PWA (app CERRADA o en segundo plano)
                   └─ showNotification(aviso)
                        └─ toque → abre/enfoca la PWA → reproduce la voz
```

### Política de autoplay (fundamento del canal abierto)

Chrome solo permite audio con sonido sin gesto previo si se cumple alguna de sus
condiciones; una es *"On mobile, the user has added the site to their home
screen"* (<https://developer.chrome.com/blog/autoplay>). Consecuencia:

- **PWA abierta desde el icono de la pantalla de inicio** → el anuncio suena al
  llegar sin ningún toque. Es el modo de uso recomendado.
- **Pestaña normal del navegador** → hace falta un toque previo. Solo en este
  caso aparece "🔊 Activar voz" como respaldo.

### Requisitos de plataforma para Web Push

- Navegador de referencia: Chrome en Android (ver OQ-1). Brave: mejor
  esfuerzo, sin verificar.
- Android (Chrome): PWA en contexto seguro (ya se sirve por HTTPS de Tailscale).
- iOS/iPadOS 16.4+: Web Push solo para PWAs **añadidas a la pantalla de inicio**
  con manifest `display: standalone` (ya cumplido,
  `static/manifest.webmanifest`), y el permiso debe pedirse desde un gesto del
  usuario.

### Fronteras de confianza

- La clave privada VAPID es un secreto del servidor: se lee de entorno, nunca
  del repositorio.
- Las suscripciones push (URL de endpoint + claves) son credenciales para
  enviar notificaciones a un dispositivo: se guardan solo en el estado local
  del servidor, nunca se registran en logs.
- El contenido de la push viaja cifrado extremo a extremo (protocolo Web Push);
  el servicio push del navegador solo ve texto cifrado.

## C. Datos y estado

### Anuncio (servidor)

- **`label`**: identifica la sesión con el mismo criterio que el selector de la
  PWA: nombre = título de Herdr sin prefijo (respaldo: nombre de la carpeta;
  después, tipo de agente) + workspace (nombre de la carpeta, solo cuando el
  nombre es el título). Referencia: `agentDisplayName` y `agentWorkspaceSub`
  (`app.js:770-782`). Sustituye al criterio actual de `agent_label`
  (`watcher.py:106-113`).
- **`aviso`** (NUEVO): frase corta = `label` + estado, p. ej.
  "‹sesión› · ‹workspace› ha terminado" / "‹sesión› · ‹workspace› necesita tu
  atención". Es lo que muestra la notificación.
- **`text`**: resumen de la salida del agente. Por la paridad audio=visual
  vigente (`watcher.py:196-199`, commit `990c627`), el mp3 del anuncio locuta
  exactamente este `text`, que ya empieza por `label`.

### Payload de la push

```json
{
  "type": "transition",
  "status": "done",
  "aviso": "‹sesión› · ‹workspace› ha terminado",
  "audio_url": "/audio/ann-….mp3",
  "pane_id": "3"
}
```

El resumen (`text`) **no** viaja en la push: solo se oye o lee dentro de la
app, que descarga el mp3 tras el toque.

### Almacenamiento y retención

- **Suscripciones**: fichero JSON en el directorio de estado, junto al audio
  (patrón existente: `history.py:36` usa `settings.audio_dir` → `parent`), p.
  ej. `~/.local/state/herdr-brain/push-subscriptions.json`. Clave: endpoint.
- **Audio**: los mp3 de anuncios se borran pasada ~1 h (`watcher.py:260-261`).
  Una notificación tocada más tarde no encontrará su mp3.

### Cliente — reproducción con la app abierta

Estado `audioBlocked: boolean` por carga de página (no persistido), inicialmente
`false`:

```
ok ──(play() rechazado)──▶ blocked
blocked ──(cualquier gesto del usuario o "🔊 Activar voz")──▶ ok
```

| muted | audioBlocked | Resultado (fuera de llamada) |
|---|---|---|
| sí | — | Solo toast (actual, `app.js:1518`) |
| no | no | Intentar `play()` al llegar; voz + toast |
| no | sí | Toast persistente + "🔊 Activar voz" |

### Cliente — permiso de notificaciones

```
sin_pedir ──("🔔 Activar notificaciones", gesto)──▶ concedido | denegado
concedido → suscripción enviada al servidor
denegado  → la app no vuelve a insistir; se explica cómo activarlo en ajustes
```

## D. Seguridad y privacidad

- Nuevos endpoints de suscripción: mismo modelo de acceso que el resto de la PWA
  (hoy, solo alcanzable por Tailscale). Sin autenticación propia: fuera de
  alcance (ver no-objetivos).
- VAPID: clave privada solo en entorno del servidor; la pública se expone a la
  PWA.
- Las suscripciones caducadas o revocadas (respuesta 404/410 del servicio push)
  se eliminan.
- **Pantalla de bloqueo**: la notificación muestra solo el aviso (qué sesión y
  en qué estado), nunca contenido de la salida del agente.
- No se elude ninguna política del navegador: el permiso y el autoplay requieren
  siempre una acción real del usuario o una condición documentada del navegador.

## E. Criterios de aceptación y métricas

- **AC1** — PWA abierta desde la pantalla de inicio, sin llamada y sin tocar
  nada: cuando un agente termina, el anuncio **suena** y se muestra su toast.
- **AC2** — PWA en una pestaña normal sin haber tocado nada: el anuncio aparece
  como toast persistente con "🔊 Activar voz"; tras un toque, el siguiente
  anuncio suena al llegar.
- **AC3** — Si `play()` falla, el texto del anuncio sigue visible.
- **AC4** — Con mute activo, solo toast (sin regresión).
- **AC5** — Durante una llamada, el comportamiento es idéntico al actual.
- **AC6** — Recargar la página no abre más de una conexión SSE.
- **AC7** — Android, PWA cerrada y pantalla bloqueada: cuando un agente termina
  o se bloquea, llega una notificación de la PWA con solo el aviso; al tocarla
  se abre la app y suena la voz del anuncio.
- **AC8** — Igual que AC7 en iPhone con iOS 16.4+ y la PWA instalada en la
  pantalla de inicio.
- **AC9** — Notificación tocada después de que su mp3 se haya borrado: la app
  reproduce el aviso regenerado o, como mínimo, lo muestra.
- **AC10** — Una suscripción revocada deja de recibir envíos y se borra del
  servidor tras el primer 404/410.
- **AC11** — Sin ninguna app instalada aparte de la PWA, se cumplen AC1 y AC7.
- **AC12** — Dos sesiones del mismo agente en la misma carpeta con títulos de
  Herdr distintos producen anuncios y notificaciones distinguibles; el nombre y
  el workspace coinciden con los del selector de la PWA.
- **AC13** — La push de ntfy de `herdr-tts` mantiene su formato actual.

Métrica de éxito: cero anuncios de fin o bloqueo de agente que el usuario no
perciba, esté la app abierta o cerrada.

## Requisitos funcionales (Fase 1)

### App abierta — `app.js`

- **FR-01** — La PWA MUST suscribirse a `GET /events` durante el arranque
  (`app.js:2545`, sección boot), independientemente de la llamada.
  Estado actual: `app.js:2415`.
- **FR-02** — `openEvents()` MUST seguir siendo idempotente (guard
  `eventsOpened`, `app.js:1501`); llamarlo también desde `startCall` MUST NOT
  abrir una segunda conexión.
- **FR-03** — Cada anuncio con `audio_url` MUST intentar reproducirse en el
  momento en que llega, sin requerir llamada ni un gesto propio de la app.
- **FR-04** — Si el navegador rechaza la reproducción, la PWA MUST NOT ocultar
  el texto del anuncio, MUST pasar a `audioBlocked` y MUST mostrar
  "🔊 Activar voz". Defecto actual: `app.js:1294-1296`.
- **FR-05** — En `audioBlocked`, cualquier gesto del usuario o el botón
  "🔊 Activar voz" MUST desbloquear la reproducción para los anuncios
  siguientes. Los bloqueados MUST NOT reproducirse con retraso.
- **FR-06** — El mute existente (`MUTE_KEY`, `app.js:1493`) MUST tener
  prioridad: silenciado ⇒ solo toast.
- **FR-07** — Los anuncios MUST llegar de todos los agentes, sin filtro por
  sesión seleccionada (`watcher.py:174`).
- **FR-08** — El comportamiento durante una llamada MUST NOT cambiar
  (`app.js:1283-1287`).

### Identificación de la sesión

- **FR-09** — La etiqueta de los anuncios de la PWA MUST identificar la sesión
  con el mismo criterio que el selector (§C, `app.js:770-782`): nombre de la
  sesión + workspace. Se aplica al toast, a la voz (paridad audio=visual) y a la
  notificación Web Push. Fuente: decisión del usuario (OQ-4).
- **FR-10** — El formato de la push de ntfy de `herdr-tts` MUST NOT cambiar.
  Fuente: decisión del usuario (OQ-4).

### App cerrada — Web Push

- **FR-11** — El servidor MUST exponer la clave pública VAPID y endpoints para
  alta y baja de suscripciones push.
- **FR-12** — El servidor MUST leer la clave privada VAPID del entorno; sin
  ella, el envío de push MUST quedar desactivado y el resto de la app MUST
  seguir funcionando.
- **FR-13** — El servidor MUST persistir las suscripciones en el directorio de
  estado local y MUST eliminar las que el servicio push rechace con 404/410.
- **FR-14** — Por cada anuncio publicado por el watcher (`watcher.py:185`), el
  servidor MUST enviar una Web Push a todas las suscripciones, con el payload de
  §C. Las de `blocked` SHOULD enviarse con urgencia alta.
- **FR-15** — El envío de push MUST NOT bloquear ni ralentizar el bucle del
  watcher ni la entrega por SSE; un fallo de envío MUST quedar registrado sin
  contenido sensible y sin datos de la suscripción.
- **FR-16** — La PWA MUST ofrecer un botón "🔔 Activar notificaciones" que pida
  el permiso desde un gesto del usuario y registre la suscripción; si el permiso
  se deniega, MUST NOT volver a pedirlo automáticamente.
- **FR-17** — En iOS, si la PWA no está instalada en la pantalla de inicio, la
  PWA SHOULD explicar que las notificaciones requieren instalarla.
- **FR-18** — El service worker MUST mostrar una notificación por cada push
  recibida con **solo el aviso**. La push MUST NOT contener el resumen de la
  salida del agente. Fuente: decisión del usuario (OQ-3).
- **FR-19** — Al tocar la notificación, el service worker MUST abrir o enfocar
  la PWA y entregarle el anuncio, y la PWA MUST reproducir su voz.
- **FR-20** — Si el mp3 del anuncio ya no existe (retención ~1 h,
  `watcher.py:260-261`), la PWA SHOULD regenerar la voz a partir del aviso con
  el endpoint de TTS bajo demanda existente (`POST /tts`, `server.py:825`) y
  MUST, como mínimo, mostrar el aviso.
- **FR-21** — Al tocar la notificación, la PWA MAY seleccionar la sesión del
  anuncio (`pane_id`).

## Fase 2 — Modo "manos libres" (solo documentado, no se aborda)

**Objetivo**: con la app en segundo plano o la pantalla bloqueada, que los
anuncios **suenen sin tocar nada**.

**Enfoque conocido**: el usuario activa el modo desde un gesto; la PWA mantiene
un audio en reproducción continua y registra la Media Session API. Chrome en
Android no congela las pestañas que están reproduciendo audio, así que el SSE
sigue vivo y los anuncios suenan al llegar.

**Riesgos identificados**:
- Muestra una notificación de reproducción fija mientras está activo.
- Mayor consumo de batería; Android puede requerir batería "sin restricciones"
  para el navegador.
- Otra app de audio (música, podcast) toma el foco y pausa el modo, y la PWA
  vuelve a congelarse.
- Sin verificar: si un audio silencioso cuenta como "reproduciendo" para no
  congelar la pestaña; comportamiento en iOS.

**Antes de abordarlo**: prototipo en un dispositivo real que valide las
suposiciones anteriores. Sin ese prototipo no se planifica.

## Preguntas abiertas

- ~~**OQ-1**~~ — **Resuelta**: el navegador de referencia es **Chrome en
  Android**; los criterios de aceptación se validan en Chrome. Brave se
  soporta como mejor esfuerzo: no está verificada ni la excepción de autoplay
  de pantalla de inicio ni Web Push en Brave; si bloquea el audio, el respaldo
  "🔊 Activar voz" (FR-04, FR-05) cubre el caso.
- ~~**OQ-2**~~ — **Resuelta**: con la app abierta y visible, la notificación
  Web Push **se mantiene** (duplicado aceptado). Suprimirla exigiría una
  funcionalidad compleja (coordinación con clientes visibles bajo la
  restricción `userVisibleOnly` de Chrome) con poco valor añadido.
- ~~**OQ-3**~~ — **Resuelta**: la notificación muestra solo el aviso; el
  resumen de la salida no viaja en la push (FR-18).
- ~~**OQ-4**~~ — **Resuelta**: los anuncios de la PWA identifican la sesión
  como el selector (nombre de la sesión + workspace + estado) (FR-09); la push
  de ntfy de `herdr-tts` sigue como hasta ahora (FR-10).

## Suposiciones

- **ASSUMPTION-1** — La excepción de autoplay de Chrome para sitios añadidos a la
  pantalla de inicio aplica a la PWA tal como está instalada hoy. Verificar con
  AC1.
- **ASSUMPTION-2** — Al abrir la PWA desde el toque en la notificación, el audio
  puede reproducirse (el toque cuenta como gesto; en Android además aplica la
  excepción de pantalla de inicio). En iOS no está verificado: verificar con
  AC8.
- **ASSUMPTION-3** — El servidor tiene salida a internet para alcanzar los
  servicios push (FCM, Apple); el móvil no necesita Tailscale para recibir la
  notificación, pero sí para abrir la PWA y descargar el audio.
- **ASSUMPTION-4** — `EventSource` reconecta automáticamente tras un reinicio
  del servidor o al volver a primer plano.
- **ASSUMPTION-5** — El icono actual es solo SVG (`manifest.webmanifest`);
  puede ser necesario añadir iconos PNG para la notificación y para iOS. A
  confirmar en planificación.
- **ASSUMPTION-6** — `herdr agent list` entrega el título de la sesión y el
  `cwd` que necesita el servidor para construir la etiqueta de FR-09 (campos
  `title` y `cwd` de `AgentInfo`, `herdr.py:38-48`).

## Riesgos

- **Voz más larga**: al añadir nombre de la sesión y workspace a la etiqueta, la
  frase locutada crece (paridad audio=visual). `LABEL_MAX_CHARS` ya limita la
  etiqueta; revisar el límite en planificación.
- **Código en movimiento**: `watcher.py`, `server.py` y `app.js` están
  recibiendo cambios en paralelo; revalidar las referencias a línea antes de
  planificar.

## A resolver en planificación

- Librería de Web Push para Python (no hay ninguna en `pyproject.toml`) y
  generación de las claves VAPID.
- Ubicación del `PushSender` (junto a `watcher.py`) y cómo se engancha al punto
  de publicación sin acoplar el watcher.
- Extraer el criterio de nombre de sesión para que servidor (etiqueta) y
  cliente (selector) no diverjan.
- Matriz de tests: suscribir/borrar, poda por 404/410, envío no bloqueante,
  payload sin `text`, etiqueta de sesión, `openEvents` al arranque e
  idempotente, `audioBlocked`, mute, llamada sin cambios; prueba manual en
  Android e iOS para AC7/AC8.

## Siguiente paso

La entrega 1 (app abierta, FR-01 a FR-08) queda planificada en
`odd/tasks/announcements-without-call.md`; se implementará en otro chat con
TDD estricto, según la elección del usuario. La entrega 2 (identificación de
la sesión, FR-09 y FR-10) y la entrega 3 (Web Push, FR-11 a FR-21) aún no se
han planificado para implementación. La Fase 2 sigue siendo solo documental.
