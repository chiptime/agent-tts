**ID**: PRD-HT-16 · **Proyecto**: herdr-tts
**Prioridad final (2026-09-23)**: P2 · **Estado**: Implementada
**Dependencias**: IPC del motor (`highlight`, `scroll-info` vía `agent_tts.send_ipc_command`); entrypoints `[[panes]]` de Herdr

# PRD-HT-16 — Popup de lectura en vivo (karaoke sobre la reproducción)

**Prioridad**: P2 · **Esfuerzo estimado**: S

## Resumen

Popup de seguimiento visual de la reproducción de audio en curso: el operador abre un lector que resalta palabra a palabra lo que el daemon está diciendo, con una cabecera de progreso (porcentaje y frase actual). Es de solo lectura: nunca inicia audio ni compite con la reproducción; se acopla al canal IPC existente y desaparece sola cuando la reproducción termina.

## Problema y flujo actual

- El motor ya ofrece resaltado tipo karaoke (`highlight`) y modos zen/bionic para lecturas manuales, pero son visuales de una sola tirada: no hay forma de seguir la reproducción en curso.
- La lectura automática del daemon (auto-read) reproduce sin terminal adjunta: el operador escucha, pero no ve dónde está la locución ni qué frase sigue.
- Volver a renderizar el texto desde el host duplicaría el estado del motor; la información de posición ya vive en el IPC (`scroll-info`).

## Propuesta

Popup `tts-reader` (`[[panes]]`, `placement = "popup"`) que ejecuta el renderer `lib/herdr_reader.py` a través del comando interno `_reader`:

1. Búfer de pantalla alterna (`\x1b[?1049h` / `\x1b[?1049l` en toda salida, incluidas señales).
2. Bucle a ~8 Hz sobre `highlight`; cada 4ª iteración refresca la cabecera con `scroll-info` (`🎧 herdr reader · <pct>% · frase <n>/<m> · q close`).
3. Salida limpia con `q` / `Esc` / `Ctrl-C`, o tras 3 fallos consecutivos de fetch (reproducción terminada). Sin reproducción activa: una línea en stderr y rc 0.
4. Superficie: flag `--reader` (launcher: popup dentro de tmux, inline fuera), comando interno `_reader`, acción `open-reader`, fila `R` en el menú de voz y acción de teclado `reader_open` (por defecto `prefix+R`; sugerida `ctrl+alt+shift+r`).

## Historias de usuario

- **US-HT-16-1**: Como operador que escucha respuestas largas en automático, quiero un popup que resalte la palabra actual y muestre el progreso, para seguir la lectura sin mirar el historial del chat.
- **US-HT-16-2**: Como operador distraído un instante, quiero reabrir el lector en cualquier momento de la reproducción y que se cierre solo al terminar, sin botones de gestión ni audio duplicado.

## Verificación

Implementada el 2026-09-23: `bash -n bin/herdr-tts`, `py_compile lib/herdr_reader.py` y la batería completa `bash scripts/smoke-tests.sh` en verde (escenario 39: rutas sin reproducción, frames en vivo contra un socket IPC de prueba, delegación del flag, fila/dispatch del menú, keymap y manifiesto). El socket real del daemon nunca se toca en pruebas.

## Fuera de alcance

- Navegación por teclado dentro del lector (salto de frase/párrafo desde el popup), scroll de historial y render HTML estructurado (HT-15).
- Cambios en el motor (`agent-tts`), en los comandos IPC existentes o en el contrato de superficie (sigue en `1`).

## Reversión

Revertir `lib/herdr_reader.py`, el wiring de `bin/herdr-tts` (flag, menú, keymap, i18n), la entrada `tts-reader`/`open-reader` del manifiesto, el escenario 39 y las filas de documentación. Sin migración de datos ni estado persistente nuevo.
